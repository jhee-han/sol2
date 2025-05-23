import torch.nn as nn
from layers import *
import pdb

class PixelCNNLayer_up(nn.Module):
    def __init__(self, nr_resnet, nr_filters, resnet_nonlinearity, film=False):
        super(PixelCNNLayer_up, self).__init__()
        self.nr_resnet = nr_resnet
        # stream from pixels above
        self.u_stream = nn.ModuleList([gated_resnet(nr_filters, down_shifted_conv2d,
                                        resnet_nonlinearity, skip_connection=0, film=film)
                                            for _ in range(nr_resnet)])
        #u_stream이라는 instance 생성 
        #Modulelist는 리스트 형태로 여러 레이어 instance를 보관할 때 사용

        # stream from pixels above and to thes left
        self.ul_stream = nn.ModuleList([gated_resnet(nr_filters, down_right_shifted_conv2d,
                                        resnet_nonlinearity, skip_connection=1, film=film)
                                            for _ in range(nr_resnet)])

    def forward(self, u, ul,class_embedding):
        u_list, ul_list = [], []

        for i in range(self.nr_resnet):
            u  = self.u_stream[i](u, class_embedding = class_embedding)
            ul = self.ul_stream[i](ul, a=u + class_embedding,class_embedding=class_embedding) #ul_stream은 gated_resnet의 instance이므로 gasted_resnet의 forward가 실행됨
            u_list  += [u]
            ul_list += [ul]

        return u_list, ul_list


class PixelCNNLayer_down(nn.Module):
    def __init__(self, nr_resnet, nr_filters, resnet_nonlinearity, film=False):
        super(PixelCNNLayer_down, self).__init__()
        self.nr_resnet = nr_resnet
        # stream from pixels above
        self.u_stream  = nn.ModuleList([gated_resnet(nr_filters, down_shifted_conv2d,
                                        resnet_nonlinearity, skip_connection=1, film=film)
                                            for _ in range(nr_resnet)])

        # stream from pixels above and to thes left
        self.ul_stream = nn.ModuleList([gated_resnet(nr_filters, down_right_shifted_conv2d,
                                        resnet_nonlinearity, skip_connection=2, film=film)
                                            for _ in range(nr_resnet)])

    def forward(self, u, ul, u_list, ul_list,class_embedding):
        for i in range(self.nr_resnet):
            u  = self.u_stream[i](u, a=u_list.pop()+ class_embedding, class_embedding = class_embedding)
            class_embedding_2=class_embedding.size(1) *2
            ul = self.ul_stream[i](ul, a=torch.cat((u, ul_list.pop()), 1)+class_embedding_2, class_embedding = class_embedding) #(B, embedding_dim)

        return u, ul


class PixelCNN(nn.Module):
    def __init__(self, nr_resnet=5, nr_filters=80, nr_logistic_mix=10,
                    resnet_nonlinearity='concat_elu', input_channels=3, num_classes=4, embedding_dim=80, film=False):
        super(PixelCNN, self).__init__()
        if resnet_nonlinearity == 'concat_elu' :
            self.resnet_nonlinearity = lambda x : concat_elu(x)
        else :
            raise Exception('right now only concat elu is supported as resnet nonlinearity.')

        self.nr_filters = nr_filters
        self.input_channels = input_channels
        self.nr_logistic_mix = nr_logistic_mix
        self.right_shift_pad = nn.ZeroPad2d((1, 0, 0, 0))
        self.down_shift_pad  = nn.ZeroPad2d((0, 0, 1, 0))
        self.embedding = nn.Embedding(num_classes, nr_filters) #Embedding(4,80)
        self.early_fusion_true = True
        self.early_fusion = nn.Conv2d(3, nr_filters, kernel_size=1) 

        # if self.early_fusion_true:
        #     self.u_init = down_shifted_conv2d(nr_filters, nr_filters, filter_size=(2,3), shift_output_down=True)
        # else:
        #     self.u_init = down_shifted_conv2d(input_channels + 1, nr_filters, filter_size=(2,3), shift_output_down=True)
        # if self.early_fusion_true:
        #     # early fusion 사용: 입력 x는 self.early_fusion(x) 후 (B, nr_filters, H, W)임.
        #     # 이후 padding 채널을 붙여 (B, nr_filters+1, H, W)로 만듦.
        #     self.u_init = down_shifted_conv2d(nr_filters + 1, nr_filters, filter_size=(2,3), shift_output_down=True)
        #     self.ul_init = nn.ModuleList([
        #         down_shifted_conv2d(nr_filters + 1, nr_filters, filter_size=(1,3), shift_output_down=True),
        #         down_right_shifted_conv2d(nr_filters + 1, nr_filters, filter_size=(2,1), shift_output_right=True)
        #     ])
        # else:
        #     self.u_init = down_shifted_conv2d(input_channels + 1, nr_filters, filter_size=(2,3), shift_output_down=True)
        #     self.ul_init = nn.ModuleList([
        #         down_shifted_conv2d(input_channels + 1, nr_filters, filter_size=(1,3), shift_output_down=True),
        #         down_right_shifted_conv2d(input_channels + 1, nr_filters, filter_size=(2,1), shift_output_right=True)
        #     ])

        down_nr_resnet = [nr_resnet] + [nr_resnet + 1] * 2 #[5,6,6]
        self.down_layers = nn.ModuleList([PixelCNNLayer_down(down_nr_resnet[i], nr_filters,
                                                self.resnet_nonlinearity, film=film) for i in range(3)])

        self.up_layers   = nn.ModuleList([PixelCNNLayer_up(nr_resnet, nr_filters,
                                                self.resnet_nonlinearity, film=film) for _ in range(3)])

        self.downsize_u_stream  = nn.ModuleList([down_shifted_conv2d(nr_filters, nr_filters,
                                                    stride=(2,2)) for _ in range(2)])

        self.downsize_ul_stream = nn.ModuleList([down_right_shifted_conv2d(nr_filters,
                                                    nr_filters, stride=(2,2)) for _ in range(2)])

        self.upsize_u_stream  = nn.ModuleList([down_shifted_deconv2d(nr_filters, nr_filters,
                                                    stride=(2,2)) for _ in range(2)])

        self.upsize_ul_stream = nn.ModuleList([down_right_shifted_deconv2d(nr_filters,
                                                    nr_filters, stride=(2,2)) for _ in range(2)])

        self.u_init = down_shifted_conv2d(nr_filters + 1, nr_filters, filter_size=(2,3),
                        shift_output_down=True)

        self.ul_init = nn.ModuleList([down_shifted_conv2d(input_channels + 1, nr_filters,
                                            filter_size=(1,3), shift_output_down=True),
                                       down_right_shifted_conv2d(input_channels + 1, nr_filters,
                                            filter_size=(2,1), shift_output_right=True)])

        num_mix = 3 if self.input_channels == 1 else 10
        self.nin_out = nin(nr_filters, num_mix * nr_logistic_mix)
        self.init_padding = None


    def forward(self, x,class_labels, sample=False):
        # similar as done in the tf repo :
        class_embedding =self.embedding(class_labels)  # (B, embedding_dim) 
        class_embedding = class_embedding.view(class_embedding.size(0),class_embedding.size(1),1,1) # (B, embedding_dim,1,1)

        # #Early Fusion
        # if self.early_fusion_true:
        #     x=self.early_fusion(x)
        #     x = class_embedding + x #torch.Size([2, 80, 32, 32])
        #     # pdb.set_trace()

        # if not sample and self.init_padding is None:
        #     xs = list(x.size())
        #     self.init_padding = torch.ones(xs[0], 1, xs[2], xs[3], device=x.device)
        # if sample:
        #     xs = list(x.size())
        #     padding = torch.ones(xs[0], 1, xs[2], xs[3], device=x.device)
        #     x = torch.cat((x, padding), 1)
        # else:
        #     x = torch.cat((x, self.init_padding), 1)  # 최종 x: (B, nr_filters+1, H, W)


        if self.init_padding is not sample:
            xs = [int(y) for y in x.size()]
            padding = Variable(torch.ones(xs[0], 1, xs[2], xs[3]), requires_grad=False)
            self.init_padding = padding.cuda() if x.is_cuda else padding

        if sample :
            xs = [int(y) for y in x.size()]
            padding = Variable(torch.ones(xs[0], 1, xs[2], xs[3]), requires_grad=False)
            padding = padding.cuda() if x.is_cuda else padding
            x = torch.cat((x, padding), 1)

        ###      UP PASS    ###
        x = x if sample else torch.cat((x, self.init_padding), 1)
        u_list  = [self.u_init(x)] #(2,80,32,32)
        ul_list = [self.ul_init[0](x) + self.ul_init[1](x)] #초기 feature map생성 #(2,80,32,32)
        for i in range(3):
            # resnet block
            u_out, ul_out = self.up_layers[i](u_list[-1], ul_list[-1],class_embedding)
            u_list  += u_out
            ul_list += ul_out

            if i != 2:
                # downscale (only twice)
                u_list  += [self.downsize_u_stream[i](u_list[-1])] #i번째 downsampling layer에 가장 최근 u출력 넣는다.
                ul_list += [self.downsize_ul_stream[i](ul_list[-1])]

        ###    DOWN PASS    ###
        u  = u_list.pop()
        ul = ul_list.pop()

        for i in range(3):
            # resnet block
            u, ul = self.down_layers[i](u, ul, u_list, ul_list,class_embedding)

            # upscale (only twice)
            if i != 2 :
                u  = self.upsize_u_stream[i](u)
                ul = self.upsize_ul_stream[i](ul)

        x_out = self.nin_out(F.elu(ul)) #(2,100,32,32)
        # pdb.set_trace()

        assert len(u_list) == len(ul_list) == 0, pdb.set_trace()

        return x_out
    
    
# class random_classifier(nn.Module):
#     def __init__(self, NUM_CLASSES):
#         super(random_classifier, self).__init__()
#         self.NUM_CLASSES = NUM_CLASSES
#         self.fc = nn.Linear(3, NUM_CLASSES)
#         print("Random classifier initialized")
#         # create a folder
#         if os.path.join(os.path.dirname(__file__), 'models') not in os.listdir():
#             os.mkdir(os.path.join(os.path.dirname(__file__), 'models'))
#         torch.save(self.state_dict(), os.path.join(os.path.dirname(__file__), 'models/conditional_pixelcnn.pth'))
#     def forward(self, x, device):
#         return torch.randint(0, self.NUM_CLASSES, (x.shape[0],)).to(device)
    
# if __name__ == '__main__':
#     dummy_input = torch.randn(2, 3, 32, 32)  # (batch, channels, height, width)
#     dummy_label = torch.randint(0, 4, (2,))  # 예: 4개의 클래스 중 랜덤한 2개 클래스

#     model = PixelCNN()
#     output = model(dummy_input, dummy_label)
#     print("Output shape:", output.shape)
